
	   double x0 = bedCenter[surfSouth[bed]].x();
	   double y0 = bedCenter[surfSouth[bed]].y();
	   double z0 = bedCenter[surfSouth[bed]].z();
	   double x1 = bedCenter[surfWest[bed]].x();
	   double y1 = bedCenter[surfWest[bed]].y();
	   double z1 = bedCenter[surfWest[bed]].z();
	   double x2 = bedCenter[surfNorth[bed]].x();
	   double y2 = bedCenter[surfNorth[bed]].y();
	   double z2 = bedCenter[surfNorth[bed]].z();
	   double x3 = bedCenter[surfEast[bed]].x();
	   double y3 = bedCenter[surfEast[bed]].y();
	   double z3 = bedCenter[surfEast[bed]].z();

	   double ax = x2 - x0;
	   double ay = y2 - y0;
	   double az = z2 - z0;
	   double bx = x1 - x3;
	   double by = y1 - y3;
	   double bz = z1 - z3;

	   vx = ay * bz - az * by;
	   vy = az * bx - ax * bz;
	   vz = ax * by - ay * bx;

	   double area = Foam::sqrt(vx*vx + vy*vy + vz*vz);

	   vx /= area;
	   vy /= area;
	   vz /= area;

/*  example of velocit smoothing, removed 

	   label cellSouth = patchBed.faceCells()[surfSouth[bed]];
	   scalar avUx = U[cellSouth].x();
	   scalar avUy = U[cellSouth].y();

	   label cellNorth = patchBed.faceCells()[surfNorth[bed]];
	   avUx += U[cellNorth].x();
	   avUy += U[cellNorth].y();

	   label cellEast = patchBed.faceCells()[surfEast[bed]];
	   avUx += U[cellEast].x();
	   avUy += U[cellEast].y();

	   label cellWest = patchBed.faceCells()[surfWest[bed]];
	   avUx += U[cellWest].x();
	   avUy += U[cellWest].y();

  	   Ux = 0.5 * Ux + avUx * 0.125;
  	   Uy = 0.5 * Uy + avUy * 0.125;

*/